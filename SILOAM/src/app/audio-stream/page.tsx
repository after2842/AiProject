import AudioStreamer from "../../../components/AudioStreamer";

export default function AudioStreamPage() {
  return (
    <div className="min-h-screen bg-gray-50 py-8">
      <div className="container mx-auto px-4">
        <h1 className="text-3xl font-bold text-center mb-8">
          Audio Streaming Test
        </h1>
        <AudioStreamer />
      </div>
    </div>
  );
}
