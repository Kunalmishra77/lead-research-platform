import { typescript } from '@leadforge/config/eslint';

export default [
  { ignores: ['dist/**'] },
  ...typescript(import.meta.dirname),
  {
    // Nest modules are decorated empty classes by design.
    rules: {
      '@typescript-eslint/no-extraneous-class': ['error', { allowWithDecorator: true }],
      // Use Drizzle via @leadforge/db (ESM); a direct import loads the CJS build (dual-package hazard).
      'no-restricted-imports': [
        'error',
        { paths: [{ name: 'drizzle-orm', message: 'Import Drizzle helpers from @leadforge/db.' }] },
      ],
    },
  },
];
