/// <reference types="vite/client" />

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
