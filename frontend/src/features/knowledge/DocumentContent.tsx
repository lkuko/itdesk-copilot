import type { Chunk, DocumentPreview } from './workspaceApi';

export function ChunkText({ chunk }: { chunk: Chunk }) {
  return <>
    <p className="muted lab-chunk-meta">字符 [{chunk.start}, {chunk.end}) · {chunk.end - chunk.start} 字符</p>
    <pre className="lab-text">{chunk.content}</pre>
  </>;
}

export function DocumentContent({ document, showFirstChunk = false }: {
  document: DocumentPreview;
  showFirstChunk?: boolean;
}) {
  return <div className="kw-document-content">
    <h3>{document.title}</h3>
    <p className="muted lab-path">{document.source} · {document.version}</p>
    <details><summary>展开原文</summary><pre className="lab-text">{document.content}</pre></details>
    <ul className="lab-chunks">
      {document.chunks.map((chunk, index) => <li key={chunk.chunk_id}>
        <details open={index === 0 && showFirstChunk}><summary>片段 {index + 1} · [{chunk.start}, {chunk.end})</summary><ChunkText chunk={chunk} /></details>
      </li>)}
    </ul>
  </div>;
}
