/**
 * Lazy-load sql-formatter and cache formatted results.
 * Handles named parameter placeholders (:p1, @name) that sql-formatter
 * can't parse by temporarily replacing them before formatting.
 */

import { useState, useEffect, useRef } from 'react';

const MAX_CACHE = 200;

function detectDialect(sql: string): 'mysql' | 'postgresql' {
  return /`[a-zA-Z_]/.test(sql) ? 'mysql' : 'postgresql';
}

function sanitizeParams(sql: string): { sanitized: string; restore: (s: string) => string } {
  const extracted: string[] = [];
  const sanitized = sql.replace(
    /(?<!:)[:@]([a-zA-Z_][a-zA-Z0-9_]*)/g,
    (m) => { extracted.push(m); return `__P${extracted.length - 1}__`; }
  );
  return {
    sanitized,
    restore: (s: string) => s.replace(/__P(\d+)__/g, (_, i) => extracted[Number(i)]),
  };
}

export function useFormatSql(sql: string | null, dialect?: 'postgresql' | 'mysql'): string | null {
  const [formatted, setFormatted] = useState<string | null>(null);
  const cache = useRef(new Map<string, string>());

  const language = dialect ?? (sql ? detectDialect(sql) : 'postgresql');

  useEffect(() => {
    if (!sql) {
      setFormatted(null);
      return;
    }

    const cacheKey = `${language}:${sql}`;
    const cached = cache.current.get(cacheKey);
    if (cached) {
      setFormatted(cached);
      return;
    }

    let cancelled = false;
    import('sql-formatter').then(({ format }) => {
      if (cancelled) return;
      try {
        const { sanitized, restore } = sanitizeParams(sql);
        const result = restore(format(sanitized, {
          language,
          tabWidth: 2,
          keywordCase: 'upper',
        }));
        if (cache.current.size >= MAX_CACHE) {
          cache.current.delete(cache.current.keys().next().value!);
        }
        cache.current.set(cacheKey, result);
        setFormatted(result);
      } catch {
        setFormatted(sql);
      }
    });
    return () => { cancelled = true; };
  }, [sql, language]);

  return formatted;
}
