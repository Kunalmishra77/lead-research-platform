import { base } from '@leadforge/config/eslint';

export default [
  { ignores: ['apps/**', 'packages/**', 'services/**', 'node_modules/**', '.turbo/**'] },
  ...base,
];
