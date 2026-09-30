import { defineConfig } from 'vite';
export default defineConfig({server:{proxy:{'/api':'http://127.0.0.1:8765','/assets':'http://127.0.0.1:8765'}},build:{assetsDir:'static',chunkSizeWarningLimit:900,rollupOptions:{input:{main:'index.html',wms:'wms.html'}}}});

