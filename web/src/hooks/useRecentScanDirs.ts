import { useCallback, useState } from 'react';

const STORAGE_KEY = 'rdst_recent_scan_dirs';
const MAX_ENTRIES = 5;

function readDirs(): string[] {
  if (typeof window === 'undefined') return [];
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((v): v is string => typeof v === 'string') : [];
  } catch {
    return [];
  }
}

export function useRecentScanDirs() {
  const [recentDirs, setRecentDirs] = useState<string[]>(readDirs);

  const addRecentDir = useCallback((path: string) => {
    const trimmed = path.trim();
    if (!trimmed) return;

    setRecentDirs((prev) => {
      const next = [trimmed, ...prev.filter((d) => d !== trimmed)].slice(0, MAX_ENTRIES);
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
      return next;
    });
  }, []);

  return { recentDirs, addRecentDir };
}
