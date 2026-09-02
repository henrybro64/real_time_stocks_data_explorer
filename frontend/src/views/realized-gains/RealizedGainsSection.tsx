import { Column, Tile } from "@carbon/react";
import type { ReactNode } from "react";
import {
  RequestStateNotice,
  type RequestNoticeKind,
} from "../../components/state/RequestStateNotice";

type PageSectionProps = Readonly<{
  children: ReactNode;
  className?: string;
}>;

export function RealizedGainsPageSection({ children, className }: PageSectionProps) {
  return (
    <Column className="app-column-spacing" sm={4} md={8} lg={16}>
      {className ? <Tile className={className}>{children}</Tile> : children}
    </Column>
  );
}

type SectionHeaderProps = Readonly<{
  eyebrow: string;
  title: string;
  actions?: ReactNode;
}>;

export function RealizedGainsSectionHeader({ eyebrow, title, actions }: SectionHeaderProps) {
  return (
    <div className="app-section-header">
      <div>
        <p className="app-eyebrow">{eyebrow}</p>
        <h2>{title}</h2>
      </div>
      {actions}
    </div>
  );
}

type ScopedRequestStateNoticeProps = Readonly<{
  kind: RequestNoticeKind | null;
  scopeLabel: string;
  detail?: string | null;
}>;

export function ScopedRequestStateNotice({
  kind,
  scopeLabel,
  detail,
}: ScopedRequestStateNoticeProps) {
  if (kind == null) {
    return null;
  }
  return (
    <div className="app-state-notice-wrap">
      <RequestStateNotice
        kind={kind}
        scopeLabel={scopeLabel}
        detail={kind === "validation" ? detail : null}
      />
    </div>
  );
}
