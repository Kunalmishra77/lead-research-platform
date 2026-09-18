import js from '@eslint/js';
import prettier from 'eslint-config-prettier';
import simpleImportSort from 'eslint-plugin-simple-import-sort';
import globals from 'globals';
import tseslint from 'typescript-eslint';

const importOrder = {
  plugins: { 'simple-import-sort': simpleImportSort },
  rules: {
    'simple-import-sort/imports': 'error',
    'simple-import-sort/exports': 'error',
  },
};

/** Syntax-only rules for JS/TS config and script files. */
export const base = [
  js.configs.recommended,
  ...tseslint.configs.recommended,
  importOrder,
  {
    languageOptions: { globals: { ...globals.node } },
    rules: {
      '@typescript-eslint/no-explicit-any': 'error',
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
    },
  },
  prettier,
];

/**
 * Type-aware rules for TypeScript packages. `tsconfigRootDir` must be the package directory.
 * @param {string} tsconfigRootDir
 */
export function typescript(tsconfigRootDir) {
  return [
    js.configs.recommended,
    ...tseslint.configs.strictTypeChecked,
    importOrder,
    {
      languageOptions: {
        globals: { ...globals.node },
        parserOptions: { projectService: true, tsconfigRootDir },
      },
      rules: {
        '@typescript-eslint/no-explicit-any': 'error',
        '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
        '@typescript-eslint/restrict-template-expressions': ['error', { allowNumber: true }],
      },
    },
    { files: ['**/*.js', '**/*.mjs', '**/*.cjs'], ...tseslint.configs.disableTypeChecked },
    prettier,
  ];
}
