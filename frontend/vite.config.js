import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
import { fileURLToPath, URL } from 'node:url';
export default defineConfig(function () {
    // 联调可覆盖后端地址：BJT_BACKEND_URL=http://127.0.0.1:8010 npm run dev
    // （默认 8000；bjt.ps1 起 frontend 时会自动注入与后端端口一致的值。）
    var backend = process.env.BJT_BACKEND_URL || 'http://localhost:8000';
    return {
        plugins: [vue()],
        resolve: {
            alias: {
                '@': fileURLToPath(new URL('./src', import.meta.url))
            }
        },
        server: {
            port: 3000,
            proxy: {
                '/api': {
                    target: backend,
                    changeOrigin: true
                },
                '/files': {
                    target: backend,
                    changeOrigin: true
                }
            }
        }
    };
});
