"use client";

import { Tab, TabList, TabPanel, TabPanels, Tabs, Tile } from "@carbon/react";
import { useState } from "react";

import { useLanguage } from "../../app/i18n";
import { InstitutionalInvestorsPanel } from "./InstitutionalInvestorsPanel";
import { MarginShortPanel } from "./MarginShortPanel";
import { OwnershipConcentrationPanel } from "./OwnershipConcentrationPanel";
import { ProfitMarginsPanel } from "./ProfitMarginsPanel";

type Props = {
  stockId: string;
};

export function TaiwanSignalsPanel({ stockId }: Readonly<Props>) {
  const { t } = useLanguage();
  const [selectedIndex, setSelectedIndex] = useState(0);

  return (
    <Tile className="app-section-tile" data-testid="tw-stock-signals-section">
      <div className="app-section-header">
        <div>
          <p className="app-eyebrow">{t("detail.twSignals.eyebrow")}</p>
          <h2>{t("detail.twSignals.title")}</h2>
        </div>
        <p className="app-section-meta">{t("detail.twSignals.description")}</p>
      </div>
      <Tabs
        onChange={({ selectedIndex: nextIndex }) => setSelectedIndex(nextIndex)}
        selectedIndex={selectedIndex}
      >
        <TabList aria-label={t("detail.twSignals.title")} contained>
          <Tab>{t("detail.twSignals.institutionalTitle")}</Tab>
          <Tab>{t("detail.twSignals.marginShortTitle")}</Tab>
          <Tab>{t("detail.twSignals.ownershipTitle")}</Tab>
          <Tab>{t("detail.twSignals.profitMarginsTitle")}</Tab>
        </TabList>
        <TabPanels>
          <TabPanel data-testid="tw-stock-signals-institutional-panel">
            <InstitutionalInvestorsPanel stockId={stockId} />
          </TabPanel>
          <TabPanel data-testid="tw-stock-signals-margin-short-panel">
            <MarginShortPanel stockId={stockId} />
          </TabPanel>
          <TabPanel data-testid="tw-stock-signals-ownership-panel">
            <OwnershipConcentrationPanel active={selectedIndex === 2} stockId={stockId} />
          </TabPanel>
          <TabPanel data-testid="tw-stock-signals-profit-margins-panel">
            <ProfitMarginsPanel active={selectedIndex === 3} stockId={stockId} />
          </TabPanel>
        </TabPanels>
      </Tabs>
    </Tile>
  );
}
