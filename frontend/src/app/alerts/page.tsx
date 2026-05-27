import { AlertsPage } from "../../views/alerts/AlertsPage";

type Props = {
  searchParams: Promise<{
    stockId?: string;
  }>;
};

export default async function Page({ searchParams }: Props) {
  const params = await searchParams;
  return <AlertsPage initialStockId={params.stockId ?? ""} />;
}
