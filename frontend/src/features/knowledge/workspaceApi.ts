import { fetchJson } from '../../api/client';

export const strategyLabels = { boundary: '优先自然边界', fixed: '固定字符窗口' };
export const searchLabels = { exact_vector: '精确匹配 + 向量', vector: '仅向量' };

export type DocumentSummary = { doc_id: string; title: string; source: string; version: string };
export type Chunk = { chunk_id: string; content: string; start: number; end: number };
export type Catalog = {
  documents: (DocumentSummary & { chunk_count: number })[];
};
export type DocumentPreview = DocumentSummary & { content: string; chunks: Chunk[] };
export type Profile = {
  embedding_model: string;
  chunk_strategy: 'boundary' | 'fixed';
  chunk_size: number;
  chunk_overlap: number;
  search_mode: 'exact_vector' | 'vector';
  top_k: number;
  score_threshold: number;
  rerank_model: string | null;
  rerank_top_n: number;
};
export type Workspace = {
  profile: Profile;
  embedding_models: string[];
  rerank_models: string[];
  revision: string;
  managed_ids: string[];
  index: { status: string; message: string };
  rebuilt?: boolean | null;
};
export type ImportFile = { name: string; content: string };
export type Match = DocumentSummary & {
  chunk_id: string;
  content: string;
  similarity: number | null;
  rerank_score: number | null;
  exact_match: boolean;
};
export type SearchResponse = {
  hits: Match[];
  rerank_status: 'disabled' | 'applied' | 'fallback' | 'no_candidates';
  embedding_tokens: number | null;
};

const KNOWLEDGE = '/api/knowledge';
const WORKSPACE = `${KNOWLEDGE}/workspace`;

export const knowledgeApi = {
  getWorkspace: () => fetchJson<Workspace>(WORKSPACE),
  catalog: () => fetchJson<Catalog>(KNOWLEDGE),
  previewDocument: (id: string) => fetchJson<DocumentPreview>(`${KNOWLEDGE}/documents/${encodeURIComponent(id)}`),
  previewImport: (profile: Profile, files: ImportFile[]) => fetchJson<{ documents: DocumentPreview[] }>(`${WORKSPACE}/preview`, {
    method: 'POST', body: { profile, files },
  }),
  apply: (profile: Profile, revision: string, files: ImportFile[], removeIds: string[]) => fetchJson<Workspace>(`${WORKSPACE}/apply`, {
    method: 'POST', body: { profile, files, remove_ids: removeIds, revision }, timeoutMs: 135_000,
  }),
  search: (query: string) => fetchJson<SearchResponse>(`${WORKSPACE}/search`, { method: 'POST', body: { query } }),
};
