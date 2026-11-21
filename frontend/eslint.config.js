// ตรวจรูปแบบโค้ดหน้าเว็บ (CI รัน `npm run lint`)
import js from '@eslint/js';
import react from 'eslint-plugin-react';
import reactHooks from 'eslint-plugin-react-hooks';
import globals from 'globals';

export default [
  { ignores: ['dist/**', 'node_modules/**', 'playwright-report/**', 'test-results/**'] },
  js.configs.recommended,
  {
    files: ['src/**/*.{js,jsx}', 'e2e/**/*.js', '*.config.js'],
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      parserOptions: { ecmaFeatures: { jsx: true } },
      globals: { ...globals.browser, ...globals.node }
    },
    plugins: { react, 'react-hooks': reactHooks },
    settings: { react: { version: '18.3' } },
    rules: {
      ...react.configs.recommended.rules,
      ...react.configs['jsx-runtime'].rules,
      ...reactHooks.configs.recommended.rules,
      'react/prop-types': 'off',          // ไม่ใช้ PropTypes (โปรเจกต์ JS ล้วน)
      'react/no-unescaped-entities': 'off', // ข้อความไทยมีเครื่องหมายคำพูดเป็นเรื่องปกติ
      'no-unused-vars': ['error', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }]
    }
  },
  {
    files: ['src/**/*.test.{js,jsx}'],
    languageOptions: { globals: { ...globals.browser, ...globals.node, ...globals.vitest } }
  }
];
