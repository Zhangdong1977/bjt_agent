/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE?: string
  readonly VITE_OPERATE_FRONTEND_BASE_URL?: string
  readonly VITE_OFFICIAL_SITE_URL?: string
  readonly VITE_REGISTER_PROMOTER_CODE?: string
}

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<{}, {}, any>
  export default component
}

declare module 'vue3-markdown' {
  import type { DefineComponent } from 'vue'
  export const VMarkdownView: DefineComponent<{ content?: string; mode?: string }, {}, any>
  export const VMarkdownEditor: DefineComponent<{ content?: string; onUpdate?: (val: string) => void }, {}, any>
}
