import { StockDetailPage } from "../../../views/stock-detail/StockDetailPage";

type Props = {
  params: Promise<{
    stockId: string;
  }>;
};

export default async function Page({ params }: Props) {
  const { stockId } = await params;
  return <StockDetailPage stockId={stockId} />;
}
