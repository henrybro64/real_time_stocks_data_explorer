import Link from "next/link";

type LeftNavLinkProps = Readonly<{
  href: string;
  label: string;
  pathname: string;
}>;

export function LeftNavLink({ href, label, pathname }: LeftNavLinkProps) {
  return (
    <Link
      className={pathname === href ? "app-left-nav-link active" : "app-left-nav-link"}
      href={href}
    >
      {label}
    </Link>
  );
}
