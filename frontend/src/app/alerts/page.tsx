import { AlertsPage } from "../../views/alerts/AlertsPage";

type Props = {
  searchParams: Promise<{
    stockId?: string | string[];
  }>;
};

export default async function Page({ searchParams }: Props) {
  const params = await searchParams;
  const stockId = Array.isArray(params.stockId)
    ? params.stockId[0] ?? ""
    : params.stockId ?? "";

  return <AlertsPage initialStockId={stockId} />;
}
