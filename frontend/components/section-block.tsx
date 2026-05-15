"use client";
import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";

import type { DeltaSection } from "@/lib/types";
import { CitationBadge } from "./citation-badge";

export function SectionBlock({ delta }: { delta: DeltaSection }) {
  return (
    <section className="flex flex-col gap-2">
      <h3 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">
        {delta.title}
      </h3>
      <div className="prose prose-sm dark:prose-invert max-w-none">
        <ReactMarkdown rehypePlugins={[rehypeSanitize]}>
          {delta.markdown}
        </ReactMarkdown>
      </div>
      {delta.citations.length > 0 && (
        <div className="flex flex-wrap gap-1 pt-1">
          {delta.citations.map((c, i) => (
            <CitationBadge key={i} citation={c} />
          ))}
        </div>
      )}
    </section>
  );
}
