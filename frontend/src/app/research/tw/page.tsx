import { TwResearchPage } from "../../../views/research/TwResearchPage";

type Props = {
  searchParams: Promise<{
    scan?: string | string[];
  }>;
};

export default async function TwResearchRoute({ searchParams }: Props) {
  const params = await searchParams;
  const scan = Array.isArray(params.scan) ? params.scan[0] ?? null : params.scan ?? null;
  return <TwResearchPage initialScanId={scan} />;
}
