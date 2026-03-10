/**
 * Lazy-load sql-formatter and cache formatted results.
 * Shared hook used by ScanResultsTable and ScanAnalysisTable.
 */

import { useState, useEffect, useRef } from 'react';

export function useFormatSql(sql: string | null): string | null {
  const [formatted, setFormatted] = useState<string | null>(null);
  const cache = useRef<Map<string, string>>(new Map());

  useEffect(() => {
    if (!sql) {
      setFormatted(null);
      return;
    }

    const cached = cache.current.get(sql);
    if (cached) {
      setFormatted(cached);
      return;
    }

    let cancelled = false;
    import('sql-formatter').then(({ format }) => {
      if (cancelled) return;
      try {
        const result = format(sql, {
          language: 'postgresql',
          tabWidth: 2,
          keywordCase: 'upper',
        });
        cache.current.set(sql, result);
        setFormatted(result);
      } catch {
        setFormatted(sql);
      }
    });
    return () => { cancelled = true; };
  }, [sql]);

  return formatted;
}
