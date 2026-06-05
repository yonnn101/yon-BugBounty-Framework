/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Default `/api` in Docker build; override for absolute API origin. */
  readonly VITE_API_BASE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
