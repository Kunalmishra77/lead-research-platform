import swc from 'unplugin-swc';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  // Nest DI needs emitDecoratorMetadata, which only the SWC transform provides in Vitest.
  plugins: [swc.vite({ module: { type: 'es6' } })],
  test: {
    include: ['test/**/*.test.ts', 'src/**/*.test.ts'],
    env: { NODE_ENV: 'test' },
  },
});
