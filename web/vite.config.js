import { defineConfig } from 'vite';
export default defineConfig({
  resolve: { alias: { vue: 'vue/dist/vue.esm-bundler.js' } },
  server: { proxy: { '/api': 'http://127.0.0.1:8090', '/audio.mp3': 'http://127.0.0.1:8090' } },
});
