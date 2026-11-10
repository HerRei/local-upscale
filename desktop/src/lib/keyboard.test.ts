import { expect, it } from 'vitest';
import { tabFromKey } from './keyboard';

it('supports arrow, Home and End navigation between tabs', () => {
  expect(tabFromKey('ArrowRight', 0, 2)).toBe(1);
  expect(tabFromKey('ArrowLeft', 0, 2)).toBe(1);
  expect(tabFromKey('Home', 1, 2)).toBe(0);
  expect(tabFromKey('End', 0, 2)).toBe(1);
  expect(tabFromKey('Enter', 0, 2)).toBeNull();
});
