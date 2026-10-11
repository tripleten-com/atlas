import { Compass } from "lucide-react";
import { Empty } from "../components";
import { useDocumentTitle } from "../hooks";

export default function NotFound() {
  useDocumentTitle("Not found");
  return (
    <div className="card mx-auto mt-10 max-w-lg">
      <Empty
        icon={Compass}
        title="There's no page here"
        msg="The link may be out of date, or the record belongs to another organisation."
        link={{ to: "/dashboard", label: "Back to the overview" }}
      />
    </div>
  );
}
