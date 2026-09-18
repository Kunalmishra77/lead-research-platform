import { typescript } from '@leadforge/config/eslint';

export default [{ ignores: ['dist/**', 'migrations/**'] }, ...typescript(import.meta.dirname)];
