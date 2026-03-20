import { useEffect, useState } from "react";

const STORAGE_KEY = "stock-ticker-watchlist";
const DEFAULT_IDS = ["US:AAPL", "TW:2330"];

export function useLocalWatchlist() {
  const [ids, setIds] = useState<string[]>(DEFAULT_IDS);

  useEffect(() => {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored) {
      try {
        const parsed = JSON.parse(stored) as string[];
        if (Array.isArray(parsed)) {
          setIds(parsed);
        }
      } catch {
        window.localStorage.removeItem(STORAGE_KEY);
      }
    }
  }, []);

  useEffect(() => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(ids));
  }, [ids]);

  const toggle = (stockId: string) => {
    setIds((current) =>
      current.includes(stockId)
        ? current.filter((item) => item !== stockId)
        : [...current, stockId],
    );
  };

  const remove = (stockId: string) => {
    setIds((current) => current.filter((item) => item !== stockId));
  };

  return { ids, toggle, remove };
}
