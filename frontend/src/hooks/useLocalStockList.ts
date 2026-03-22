import { useSyncExternalStore } from "react";

type ListConfig = {
  storageKey: string;
  defaultIds: string[];
  eventName: string;
};

function createLocalStockListHook({ storageKey, defaultIds, eventName }: ListConfig) {
  let listIds: string[] = defaultIds;
  const listeners = new Set<() => void>();

  function readStoredIds(): string[] {
    if (typeof window === "undefined") {
      return defaultIds;
    }

    const stored = window.localStorage.getItem(storageKey);
    if (!stored) {
      return defaultIds;
    }

    try {
      const parsed = JSON.parse(stored) as unknown;
      return Array.isArray(parsed)
        ? parsed.filter((item): item is string => typeof item === "string")
        : defaultIds;
    } catch {
      window.localStorage.removeItem(storageKey);
      return defaultIds;
    }
  }

  function emitChange() {
    listeners.forEach((listener) => listener());
  }

  function setIds(nextIds: string[]) {
    listIds = nextIds;

    if (typeof window !== "undefined") {
      window.localStorage.setItem(storageKey, JSON.stringify(nextIds));
      window.dispatchEvent(new Event(eventName));
    }

    emitChange();
  }

  function subscribe(callback: () => void) {
    listeners.add(callback);

    if (typeof window !== "undefined") {
      const onStorage = (e: StorageEvent) => {
        if (e.key !== storageKey) {
          return;
        }
        listIds = readStoredIds();
        emitChange();
      };

      const onListUpdated = () => {
        listIds = readStoredIds();
        emitChange();
      };

      window.addEventListener("storage", onStorage);
      window.addEventListener(eventName, onListUpdated);

      return () => {
        listeners.delete(callback);
        window.removeEventListener("storage", onStorage);
        window.removeEventListener(eventName, onListUpdated);
      };
    }

    return () => {
      listeners.delete(callback);
    };
  }

  function getSnapshot() {
    return listIds;
  }

  function getServerSnapshot() {
    return defaultIds;
  }

  return function useLocalStockList() {
    if (typeof window !== "undefined") {
      listIds = readStoredIds();
    }
    const ids = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

    const toggle = (stockId: string) => {
      const nextIds = ids.includes(stockId) ? ids.filter((item) => item !== stockId) : [...ids, stockId];
      setIds(nextIds);
    };

    const remove = (stockId: string) => {
      setIds(ids.filter((item) => item !== stockId));
    };

    return { ids, toggle, remove };
  };
}

/** Symbols you track for study / alerts (dashboard watchlist). */
export const useLocalWatchlist = createLocalStockListHook({
  storageKey: "stock-ticker-watchlist",
  defaultIds: ["US:AAPL", "TW:2330"],
  eventName: "stock-ticker-watchlist-updated",
});

/** Symbols you mark as actual holdings (portfolio page only). */
export const useLocalPortfolio = createLocalStockListHook({
  storageKey: "stock-ticker-portfolio",
  defaultIds: [],
  eventName: "stock-ticker-portfolio-updated",
});
