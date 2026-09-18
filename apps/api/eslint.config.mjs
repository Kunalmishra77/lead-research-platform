import { typescript } from '@leadforge/config/eslint';

export default [
  { ignores: ['dist/**'] },
  ...typescript(import.meta.dirname),
  {
    // Nest modules are decorated empty classes by design.
    rules: { '@typescript-eslint/no-extraneous-class': ['error', { allowWithDecorator: true }] },
  },
];
