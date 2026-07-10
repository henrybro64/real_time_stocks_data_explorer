"use client";

import { Tab, TabList, TabPanel, TabPanels, Tabs, Tile } from "@carbon/react";
import { useLanguage } from "../../app/i18n";
import { InstitutionalInvestorsPanel } from "./InstitutionalInvestorsPanel";

type Props = {
  stockId: string;
};

export function TaiwanSignalsPanel({ stockId }: Readonly<Props>) {
  const { t } = useLanguage();

  return (
    <Tile className="app-section-tile" data-testid="tw-stock-signals-section">
      <div className="app-section-header">
        <div>
          <p className="app-eyebrow">{t("detail.twSignals.eyebrow")}</p>
          <h2>{t("detail.twSignals.title")}</h2>
        </div>
        <p className="app-section-meta">{t("detail.twSignals.description")}</p>
      </div>
      <Tabs>
        <TabList aria-label={t("detail.twSignals.title")} contained>
          <Tab>{t("detail.twSignals.institutionalTitle")}</Tab>
          <Tab>{t("detail.twSignals.marginShortTitle")}</Tab>
        </TabList>
        <TabPanels>
          <TabPanel data-testid="tw-stock-signals-institutional-panel">
            <InstitutionalInvestorsPanel stockId={stockId} />
          </TabPanel>
          <TabPanel data-testid="tw-stock-signals-margin-short-panel">
            <p className="app-supporting-copy">{t("detail.twSignals.marginShortPlaceholder")}</p>
          </TabPanel>
        </TabPanels>
      </Tabs>
    </Tile>
  );
}
