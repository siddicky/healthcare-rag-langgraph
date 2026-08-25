"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import "./markdown.css";

export function Markdown({ content }: { content: string }) {
  if (content === "") return null;
  return (
    <div className="md-root">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: ({ children }) => <h1 className="md-h1">{children}</h1>,
          h2: ({ children }) => <h2 className="md-h2">{children}</h2>,
          h3: ({ children }) => <h3 className="md-h3">{children}</h3>,
          h4: ({ children }) => <h4 className="md-h4">{children}</h4>,
          p: ({ children }) => <p className="md-p">{children}</p>,
          ul: ({ children }) => <ul className="md-ul">{children}</ul>,
          ol: ({ children }) => <ol className="md-ol">{children}</ol>,
          li: ({ children }) => <li className="md-li">{children}</li>,
          a: ({ href, children }) => (
            <a className="md-a" href={href} target="_blank" rel="noopener noreferrer">
              {children}
            </a>
          ),
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          code: (props: any) => {
            const { children, className } = props as { children: React.ReactNode; className?: string };
            const isBlock = typeof className === "string" && className.startsWith("language-");
            return <code className={isBlock ? `md-code-block ${className}` : "md-code-inline"}>{children}</code>;
          },
          pre: ({ children }) => <pre className="md-pre">{children}</pre>,
          blockquote: ({ children }) => <blockquote className="md-blockquote">{children}</blockquote>,
          table: ({ children }) => <table className="md-table">{children}</table>,
          th: ({ children }) => <th className="md-th">{children}</th>,
          td: ({ children }) => <td className="md-td">{children}</td>,
          hr: () => <hr className="md-hr" />,
          strong: ({ children }) => <strong className="md-strong">{children}</strong>,
          em: ({ children }) => <em className="md-em">{children}</em>,
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
