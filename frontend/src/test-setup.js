import {afterEach, vi} from 'vitest';
import {enableAutoUnmount} from '@vue/test-utils';

enableAutoUnmount(afterEach);
HTMLDialogElement.prototype.showModal = function () { this.open = true; };
HTMLDialogElement.prototype.close = function () { this.open = false; };
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });
