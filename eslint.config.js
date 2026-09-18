import { base } from '@leadforge/config/eslint';

export default [
  // Workspace packages are linted by their own configs via `turbo run lint`.
  {
    ignores: [
      'apps/**',
      'packages/**',
      'infra/**',
      'db/**',
      'services/**',
      'node_modules/**',
      '.turbo/**',
    ],
  },
  ...base,
  // lint-staged lints files of several packages in one process; pin the root explicitly.
  { languageOptions: { parserOptions: { tsconfigRootDir: import.meta.dirname } } },
];
