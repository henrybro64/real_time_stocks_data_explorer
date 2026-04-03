"use client";

import { Column, Grid, Tile } from "@carbon/react";
import { AiKeyPanel } from "../../components/layout/AiKeyPanel";
import { useLanguage } from "../../app/i18n";

export function AiSettingsPage() {
  const { t } = useLanguage();

  return (
    <Grid className="app-page-grid" fullWidth>
      <Column className="app-column-spacing" sm={4} md={8} lg={16}>
        <Tile className="app-hero-tile">
          <div className="app-hero-content">
            <h1 className="app-hero-title">{t("aiSettings.title")}</h1>
            <p className="app-body-copy app-hero-description">{t("aiSettings.description")}</p>
          </div>
        </Tile>
      </Column>
      <Column className="app-column-spacing" sm={4} md={8} lg={16}>
        <Tile className="app-section-tile">
          <AiKeyPanel />
        </Tile>
      </Column>
    </Grid>
  );
}
