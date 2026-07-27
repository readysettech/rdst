import { beforeEach } from 'vitest'

// jsdom under this vitest setup does not expose a working Storage — accessing
// window.localStorage.getItem/setItem/clear throws "is not a function". Components
// that persist small UI preferences (the Sidebar advanced-section toggle, the demo
// walkthrough flag) read it at render, so install an in-memory Storage for both
// localStorage and sessionStorage. Cleared before each test so state never leaks.
class MemoryStorage {
  private store = new Map<string, string>()
  get length(): number {
    return this.store.size
  }
  clear(): void {
    this.store.clear()
  }
  getItem(key: string): string | null {
    return this.store.has(key) ? (this.store.get(key) as string) : null
  }
  key(index: number): string | null {
    return Array.from(this.store.keys())[index] ?? null
  }
  removeItem(key: string): void {
    this.store.delete(key)
  }
  setItem(key: string, value: string): void {
    this.store.set(key, String(value))
  }
}

for (const name of ['localStorage', 'sessionStorage'] as const) {
  Object.defineProperty(globalThis, name, {
    value: new MemoryStorage() as unknown as Storage,
    configurable: true,
    writable: true,
  })
}

// jsdom has no ResizeObserver, and Radix primitives (ScrollArea, Select) probe
// for it at mount. Defined rather than vi.stubGlobal'd so suites that call
// vi.unstubAllGlobals() keep it.
Object.defineProperty(globalThis, 'ResizeObserver', {
  value: class {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  },
  configurable: true,
  writable: true,
})

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
})
