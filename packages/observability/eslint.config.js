import { typescript } from '@leadforge/config/eslint';

export default [{ ignores: ['dist/**'] }, ...typescript(import.meta.dirname)];
