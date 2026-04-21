import type { RequestNoticeKind } from "../../components/state/RequestStateNotice";
import { errorToNoticeKind } from "../../components/state/RequestStateNotice";

type DeriveWatchlistNoticeKindOptions = {
  hasLoaded: boolean;
  mutationError: unknown;
  queryError: unknown;
  quoteError: unknown;
  quoteCount: number;
};

export function deriveWatchlistNoticeKind({
  hasLoaded,
  mutationError,
  queryError,
  quoteError,
  quoteCount,
}: DeriveWatchlistNoticeKindOptions): RequestNoticeKind | null {
  if (mutationError) {
    return errorToNoticeKind(mutationError);
  }
  if (queryError && !hasLoaded) {
    return errorToNoticeKind(queryError);
  }
  if (quoteError) {
    return quoteCount > 0 ? "stale" : errorToNoticeKind(quoteError);
  }
  return null;
}
