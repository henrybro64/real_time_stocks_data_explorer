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
import { Menu, Notification } from "@carbon/icons-react";
import { type ReactNode, useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { LeftNavLink } from "./LeftNavLink";
import { useLanguage } from "../../app/i18n";
import { fetchAuthStatus, logout } from "../../services/api/auth";
import type { AuthStatus } from "../../services/api/auth";

type Props = {
  children: ReactNode;
};

export function Shell({ children }: Props) {
  const pathname = usePathname();
  const router = useRouter();
  const { language, setLanguage, t } = useLanguage();
  const [isLeftPanelVisible, setIsLeftPanelVisible] = useState(true);
  const [auth, setAuth] = useState<AuthStatus | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchAuthStatus()
      .then((s) => {
        if (!cancelled) {
          setAuth(s);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setAuth(null);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [pathname]);

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
                  <HeaderMenuItem as={Link} href="/alerts">
                    {t("nav.alerts")}
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
                  {auth?.authEnabled ? (
                    <div className="app-auth-actions">
                      {auth.loggedIn && auth.user ? (
                        <>
                          <span className="cds--label-01 app-auth-user" title={auth.user.username}>
                            {auth.user.username}
                          </span>
                          <Button
                            kind="ghost"
                            size="sm"
                            onClick={async () => {
                              try {
                                await logout();
                              } finally {
                                setAuth((current) =>
                                  current
                                    ? {
                                        ...current,
                                        loggedIn: false,
                                        user: null,
                                      }
                                    : current,
                                );
                                router.replace("/login");
                                router.refresh();
                              }
                            }}
                          >
                            Sign out
                          </Button>
                        </>
                      ) : (
                        <Button
                          kind="ghost"
                          size="sm"
                          onClick={() => {
                            router.push("/login");
                          }}
                        >
                          Sign in
                        </Button>
                      )}
                    </div>
                  ) : null}
                  <HeaderGlobalAction aria-label={t("header.notifications")} tooltipAlignment="end">
                    <Notification size={20} />
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
                          <LeftNavLink href="/" label={t("nav.dashboard")} pathname={pathname} />
                          <a className="app-left-nav-link" href="/#markets">
                            {t("nav.markets")}
                          </a>
                          <LeftNavLink href="/alerts" label={t("nav.alerts")} pathname={pathname} />
                          <LeftNavLink href="/rankings" label={t("nav.rankings")} pathname={pathname} />
                          <a className="app-left-nav-link" href="/#watchlist">
                            {t("nav.watchlist")}
                          </a>
                          <LeftNavLink href="/portfolio" label={t("nav.portfolio")} pathname={pathname} />
                          <LeftNavLink
                            href="/realized-gains"
                            label={t("nav.realizedGains")}
                            pathname={pathname}
                          />
                          <LeftNavLink href="/assets" label={t("nav.assets")} pathname={pathname} />
                          <LeftNavLink href="/ai-settings" label={t("nav.aiSettings")} pathname={pathname} />
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
