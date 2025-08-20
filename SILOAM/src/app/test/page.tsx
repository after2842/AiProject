"use client";

import { useRef, useState } from "react";

export default function DraftCounter() {
  const [shown, setShown] = useState(0); // what the UI shows
  const hiddenCount = useRef(0); // “scratchpad” value

  const bumpHidden = () => {
    hiddenCount.current += 1;
  };
  const commitToUI = () => setShown(hiddenCount.current);

  return (
    <div>
      <div>Shown count: {shown}</div>
      <button onClick={bumpHidden}>Add (hidden)</button>
      <button onClick={commitToUI}>Commit to UI</button>
    </div>
  );
}
