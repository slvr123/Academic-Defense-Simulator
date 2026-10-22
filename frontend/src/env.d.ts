/// <reference types="vite/client" />

// Values here are baked into the public JavaScript at build time: never a secret.
interface ImportMetaEnv {
  readonly VITE_API_BASE_URL: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
