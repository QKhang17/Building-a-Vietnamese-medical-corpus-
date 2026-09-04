import { defineConfig } from 'vite'
import { copyFileSync } from 'node:fs'
import { resolve } from 'node:path'

// https://vite.dev/config/
export default defineConfig({
  // Serve MedNLP Studio từ thư mục public/
  root: 'public',
  publicDir: false,

  plugins: [{
    name: 'copy-classic-mednlp-script',
    writeBundle() {
      copyFileSync(
        resolve(import.meta.dirname, 'public/script.js'),
        resolve(import.meta.dirname, 'dist/script.js'),
      )
    },
  }],

  server: {
    port: 5173,
    proxy: {
      // Proxy API calls tới Python backend (port 8000)
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      // Proxy tới Spring Boot backend (port 8080) nếu cần
      '/spring': {
        target: 'http://localhost:8080',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/spring/, ''),
      },
    },
  },

  build: {
    outDir: '../dist',
    emptyOutDir: true,
    rollupOptions: {
      input: {
        index: resolve(import.meta.dirname, 'public/index.html'),
        annotation: resolve(import.meta.dirname, 'public/annotation.html'),
      },
    },
  },
})
