import { typescript } from '@leadforge/config/eslint';

export default [
  { ignores: ['dist/**', 'src/generated/**', 'generated/**', '.venv/**'] },
  ...typescript(import.meta.dirname),
];
