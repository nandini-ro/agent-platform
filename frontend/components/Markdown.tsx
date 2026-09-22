"use client";

import ReactMarkdown, { type Components } from "react-markdown";
import rehypeRaw from "rehype-raw";
import rehypeSanitize from "rehype-sanitize";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";

/* Model output is markdown, and it leans on GFM tables plus the occasional raw
   `<br>` inside a table cell. rehype-raw keeps that HTML instead of printing it
   literally; rehype-sanitize runs after it so nothing the model (or a prompt
   injected into it) emits can turn into script or a stray attribute. */
/* remark-breaks keeps a single newline a line break, the way the bubble's old
   `whitespace-pre-wrap` did — models write plain multi-line answers that markdown
   would otherwise reflow into one paragraph. */
const REMARK_PLUGINS = [remarkGfm, remarkBreaks];
const REHYPE_PLUGINS = [rehypeRaw, rehypeSanitize];

/* react-markdown hands every component the mdast node alongside the element's
   own props. It must not reach the DOM, so props go through here on the way. */
function html<P extends { className?: string }>(props: P, className: string): P {
  const rest = { ...props } as P & { node?: unknown };
  delete rest.node;
  rest.className = [className, props.className].filter(Boolean).join(" ");
  return rest;
}

/* Tailwind has no typography plugin here, so every element the model can emit
   is styled by hand. Vertical rhythm comes from `space-y-3` on the wrapper;
   these only set what that cannot (inline weight, borders, list markers). */
const COMPONENTS: Components = {
  h1: (p) => <h1 {...html(p, "mt-1 text-lg font-semibold tracking-tight text-slate-900")} />,
  h2: (p) => <h2 {...html(p, "mt-1 text-base font-semibold tracking-tight text-slate-900")} />,
  h3: (p) => <h3 {...html(p, "mt-1 text-sm font-semibold tracking-tight text-slate-900")} />,
  h4: (p) => <h4 {...html(p, "mt-1 text-sm font-semibold text-slate-700")} />,
  p: (p) => <p {...html(p, "break-words")} />,
  strong: (p) => <strong {...html(p, "font-semibold text-slate-900")} />,
  ul: (p) => <ul {...html(p, "list-disc space-y-1 pl-5 marker:text-slate-400")} />,
  ol: (p) => <ol {...html(p, "list-decimal space-y-1 pl-5 marker:text-slate-400")} />,
  li: (p) => <li {...html(p, "break-words")} />,
  blockquote: (p) => (
    <blockquote {...html(p, "border-l-2 border-slate-300 pl-3 italic text-slate-600")} />
  ),
  hr: (p) => <hr {...html(p, "border-slate-200")} />,
  a: (p) => (
    <a
      target="_blank"
      rel="noopener noreferrer"
      {...html(p, "font-medium text-slate-900 underline underline-offset-2 hover:text-slate-600")}
    />
  ),
  /* A wide table would push the bubble past its max width, so it scrolls on its
     own axis rather than widening the message. */
  table: (p) => (
    <div className="w-full overflow-x-auto">
      <table {...html(p, "w-full border-collapse text-sm")} />
    </div>
  ),
  thead: (p) => <thead {...html(p, "bg-slate-50")} />,
  th: (p) => (
    <th {...html(p, "border border-slate-200 px-2.5 py-1.5 text-left font-semibold text-slate-900")} />
  ),
  td: (p) => <td {...html(p, "border border-slate-200 px-2.5 py-1.5 align-top")} />,
  /* Inline code and fenced blocks arrive as the same element; inside a <pre>
     the block already has the padding and background, so the code drops its. */
  code: (p) => (
    <code
      {...html(
        p,
        "rounded bg-slate-100 px-1 py-0.5 font-mono text-[0.9em] text-slate-800 [pre_&]:bg-transparent [pre_&]:p-0",
      )}
    />
  ),
  pre: (p) => (
    <pre {...html(p, "overflow-x-auto rounded-lg bg-slate-100 px-3 py-2.5 text-sm leading-normal")} />
  ),
};

/* While a response streams, the caret belongs at the end of the text rather
   than on a line of its own below it, so it is drawn as an ::after on whatever
   block element happens to be last at that moment. */
const STREAMING_CARET =
  "[&>:last-child]:after:ml-0.5 [&>:last-child]:after:inline-block [&>:last-child]:after:h-4 " +
  "[&>:last-child]:after:w-1.5 [&>:last-child]:after:animate-pulse [&>:last-child]:after:bg-slate-400 " +
  "[&>:last-child]:after:align-text-bottom [&>:last-child]:after:content-['']";

export function Markdown({
  content,
  caret = false,
}: {
  content: string;
  caret?: boolean;
}) {
  return (
    <div className={`space-y-3 ${caret ? STREAMING_CARET : ""}`}>
      <ReactMarkdown
        remarkPlugins={REMARK_PLUGINS}
        rehypePlugins={REHYPE_PLUGINS}
        components={COMPONENTS}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
