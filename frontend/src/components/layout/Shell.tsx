"use client";

import {
  Button,
  Content,
  Header,
  HeaderContainer,
  HeaderGlobalAction,
  HeaderGlobalBar,
  HeaderMenuItem,
  HeaderName,
  HeaderNavigation,
  SkipToContent,
  Theme,
} from "@carbon/react";
import { Menu, Notification, UserAvatar } from "@carbon/icons-react";
import { type ReactNode, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useLanguage } from "../../app/i18n";

type Props = {
  children: ReactNode;
};

export function Shell({ children }: Props) {
  const pathname = usePathname();
  const { language, setLanguage, t } = useLanguage();
  const [isLeftPanelVisible, setIsLeftPanelVisible] = useState(true);

  return (
    <>
      <SkipToContent />
      <HeaderContainer
        render={() => (
          <>
            <Theme as="div" className="app-shell-header" theme="g100">
              <Header aria-label="Stock Ticker">
                <HeaderName href="/" prefix="Stock">
                  Ticker
                </HeaderName>
                <HeaderNavigation aria-label="Primary navigation">
                  <HeaderMenuItem as={Link} href="/">
                    {t("nav.dashboard")}
                  </HeaderMenuItem>
                </HeaderNavigation>
                <HeaderGlobalBar>
                  <div className="app-language-toggle" aria-label={t("header.language")}>
                    <Button
                      kind={language === "en" ? "primary" : "ghost"}
                      size="sm"
                      onClick={() => setLanguage("en")}
                    >
                      EN
                    </Button>
                    <Button
                      kind={language === "zh-TW" ? "primary" : "ghost"}
                      size="sm"
                      onClick={() => setLanguage("zh-TW")}
                    >
                      繁中
                    </Button>
                  </div>
                  <HeaderGlobalAction aria-label={t("header.notifications")} tooltipAlignment="end">
                    <Notification size={20} />
                  </HeaderGlobalAction>
                  <HeaderGlobalAction aria-label={t("header.userProfile")} tooltipAlignment="end">
                    <UserAvatar size={20} />
                  </HeaderGlobalAction>
                </HeaderGlobalBar>
              </Header>
            </Theme>
            <Content id="main-content" className="app-content">
              <Theme as="div" className="app-page-theme" theme="g100">
                <div className={isLeftPanelVisible ? "app-shell-layout" : "app-shell-layout app-shell-layout-collapsed"}>
                  {isLeftPanelVisible ? (
                    <div className="app-shell-left-column">
                      <Button
                        className="app-panel-toggle"
                        kind="ghost"
                        size="sm"
                        renderIcon={Menu}
                        iconDescription={isLeftPanelVisible ? t("nav.menu.close") : t("nav.menu.open")}
                        onClick={() => setIsLeftPanelVisible((current) => !current)}
                      >
                        {isLeftPanelVisible ? "Hide panel" : "Show panel"}
                      </Button>
                      <aside className="app-shell-left-panel" aria-label="Workspace navigation">
                        <div className="app-shell-brand">
                          <p className="app-eyebrow">Workspace</p>
                          <p className="app-shell-brand-subtitle">Market command center</p>
                        </div>
                        <nav className="app-left-nav">
                          <Link className={pathname === "/" ? "app-left-nav-link active" : "app-left-nav-link"} href="/">
                            {t("nav.dashboard")}
                          </Link>
                          <a className="app-left-nav-link" href="/#markets">
                            {t("nav.markets")}
                          </a>
                          <a className="app-left-nav-link" href="/#watchlist">
                            {t("nav.watchlist")}
                          </a>
                          <Link
                            className={pathname === "/portfolio" ? "app-left-nav-link active" : "app-left-nav-link"}
                            href="/portfolio"
                          >
                            {t("nav.portfolio")}
                          </Link>
                          <Link
                            className={pathname === "/assets" ? "app-left-nav-link active" : "app-left-nav-link"}
                            href="/assets"
                          >
                            {t("nav.assets")}
                          </Link>
                          <Link
                            className={pathname === "/ai-settings" ? "app-left-nav-link active" : "app-left-nav-link"}
                            href="/ai-settings"
                          >
                            {t("nav.aiSettings")}
                          </Link>
                        </nav>
                      </aside>
                    </div>
                  ) : null}
                  <div className="app-shell-content">
                    {!isLeftPanelVisible ? (
                      <Button
                        className="app-panel-toggle app-panel-toggle-inline"
                        kind="ghost"
                        size="sm"
                        renderIcon={Menu}
                        iconDescription={t("nav.menu.open")}
                        onClick={() => setIsLeftPanelVisible(true)}
                      >
                        Show panel
                      </Button>
                    ) : null}
                    {children}
                  </div>
                </div>
              </Theme>
            </Content>
          </>
        )}
      />
    </>
  );
}
