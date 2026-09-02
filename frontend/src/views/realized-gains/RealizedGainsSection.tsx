import { Column, Tile } from "@carbon/react";
import type { ReactNode } from "react";

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
