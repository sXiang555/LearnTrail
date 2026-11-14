import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '^/ai/v1/(skills|memories)(/|\\?|$)': {
        target: 'http://127.0.0.1:8100',
        changeOrigin: true,
        rewrite: path => path.replace(/^\/ai/, '')
      },
      '/ai/v1/sessions': {
        target: 'http://127.0.0.1:8100',
        changeOrigin: true,
        proxyTimeout: 0,
        timeout: 0,
        rewrite: path => path.replace(/^\/ai/, '')
      },
      '/api': {
        target: 'http://127.0.0.1:10010',
        changeOrigin: true,
        cookieDomainRewrite: '',
        rewrite: path => path.replace(/^\/api/, ''),
        configure(proxy) {
          proxy.on('proxyReq', request => {
            request.setHeader('Host', 'api.learntrail.com')
            request.setHeader('Origin', 'http://www.learntrail.com')
          })
        }
      }
    }
  }
})
