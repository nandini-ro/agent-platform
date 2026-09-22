"""Financial Intelligence MCP server.

Provides controlled, read-only access to the synthetic financial database.

The server communicates with the Agent Platform over MCP stdio.
"""

from mcp.server import MCPServer
from sqlalchemy import create_engine, text


DATABASE_URL = (
    "postgresql+psycopg://financial_agent_user:"
    "financial_agent_dev_2026@localhost:5432/financial_agent_db"
)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
)


mcp = MCPServer(
    name="financial-intelligence",
    instructions=(
        "Provides controlled, read-only access to financial portfolio data. "
        "Tools must never expose another user's financial information."
    ),
    version="0.2.0",
)


@mcp.tool()
def financial_server_status() -> str:
    """Check whether the Financial MCP server is running correctly."""

    return "Financial Intelligence MCP server is running."


@mcp.tool()
def get_my_holdings() -> str:
    """Return the authenticated demo investor's current portfolio holdings.

    This tool intentionally accepts no user_id argument. The caller cannot
    select another user's portfolio.
    """

    authenticated_user_id = 1

    query = text(
        """
        SELECT
            h.symbol,
            c.company_name,
            c.sector,
            h.quantity,
            h.average_buy_price
        FROM users u
        JOIN accounts a
            ON a.user_id = u.id
        JOIN holdings h
            ON h.account_id = a.id
        JOIN companies c
            ON c.symbol = h.symbol
        WHERE u.id = :user_id
        ORDER BY h.symbol
        """
    )

    with engine.connect() as connection:
        rows = connection.execute(
            query,
            {"user_id": authenticated_user_id},
        ).mappings().all()

    if not rows:
        return "No holdings found."

    lines = ["Current portfolio holdings:"]

    for row in rows:
        lines.append(
            f"{row['symbol']} | "
            f"{row['company_name']} | "
            f"Sector: {row['sector']} | "
            f"Quantity: {row['quantity']} | "
            f"Average buy price: ${row['average_buy_price']}"
        )

    return "\n".join(lines)

@mcp.tool()
def get_my_watchlist() -> str:
    """Return the authenticated demo investor's watchlist.

    This tool accepts no user_id argument so the caller cannot request
    another user's watchlist.
    """

    authenticated_user_id = 1

    query = text(
        """
        SELECT
            w.symbol,
            c.company_name,
            c.sector,
            c.industry,
            w.notes
        FROM watchlists w
        JOIN companies c
            ON c.symbol = w.symbol
        WHERE w.user_id = :user_id
        ORDER BY w.symbol
        """
    )

    with engine.connect() as connection:
        rows = connection.execute(
            query,
            {"user_id": authenticated_user_id},
        ).mappings().all()

    if not rows:
        return "No companies are currently on the watchlist."

    lines = ["Current watchlist:"]

    for row in rows:
        lines.append(
            f"{row['symbol']} | "
            f"{row['company_name']} | "
            f"Sector: {row['sector']} | "
            f"Industry: {row['industry']} | "
            f"Notes: {row['notes']}"
        )

    return "\n".join(lines)

@mcp.tool()
def get_my_transactions(symbol: str = "") -> str:
    """Return the authenticated demo investor's transaction history.

    Args:
        symbol: Optional stock symbol such as AAPL or NVDA.
                Leave empty to return all transactions.

    The authenticated user is determined internally and cannot be
    selected by the caller.
    """

    authenticated_user_id = 1

    normalized_symbol = symbol.strip().upper()

    query = text(
        """
        SELECT
            t.transaction_date,
            t.symbol,
            c.company_name,
            t.transaction_type,
            t.quantity,
            t.price,
            t.notes
        FROM transactions t
        JOIN accounts a
            ON a.id = t.account_id
        JOIN companies c
            ON c.symbol = t.symbol
        WHERE a.user_id = :user_id
          AND (
              :symbol = ''
              OR t.symbol = :symbol
          )
        ORDER BY t.transaction_date DESC, t.id DESC
        """
    )

    with engine.connect() as connection:
        rows = connection.execute(
            query,
            {
                "user_id": authenticated_user_id,
                "symbol": normalized_symbol,
            },
        ).mappings().all()

    if not rows:
        if normalized_symbol:
            return f"No transactions found for {normalized_symbol}."
        return "No transactions found."

    if normalized_symbol:
        lines = [f"Transaction history for {normalized_symbol}:"]
    else:
        lines = ["Transaction history:"]

    for row in rows:
        lines.append(
            f"{row['transaction_date']} | "
            f"{row['symbol']} | "
            f"{row['transaction_type']} | "
            f"Quantity: {row['quantity']} | "
            f"Price: ${row['price']} | "
            f"Notes: {row['notes'] or 'None'}"
        )

    return "\n".join(lines)

if __name__ == "__main__":
    mcp.run(transport="stdio")