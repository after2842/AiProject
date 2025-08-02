import React, { useRef, useEffect, useState } from "react";
import { motion } from "framer-motion";
import { useRouter } from "next/navigation";
import ReactMarkdown from "react-markdown";

export default function MainPage() {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const router = useRouter();
  const [message, setMessage] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [aiResponse, setAiResponse] = useState("");
  const adjustHeight = () => {
    const textarea = textareaRef.current;
    if (textarea) {
      textarea.style.height = "auto";
      textarea.style.height = Math.min(textarea.scrollHeight, 120) + "px";
    }
  };
  const handleLogoClick = () => {
    router.push("/");
  };

  const sendMessage = async () => {
    if (!message.trim()) return;

    setIsLoading(true);
    try {
      const response = await fetch("http://localhost:5000/api/chat", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ message: message }),
      });

      const data = await response.json();

      if (data.success) {
        setAiResponse(data.response);
        setMessage("");
      } else {
        console.error("API Error:", data.error);
      }
    } catch (error) {
      console.error("Network Error:", error);
    } finally {
      setIsLoading(false);
    }
  };

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };
  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.8 }}
      animate={{ opacity: 1, scale: 1 }}
      exit={{ opacity: 1, scale: 0.8 }}
      transition={{ duration: 0.5, ease: "easeInOut" }}
      className="min-h-screen flex flex-col bg-white text-gray-800"
    >
      {/* Header */}
      <header className="w-full px-6 py-4 flex items-center justify-between">
        <button
          className="px-4 py-2 bg-pink-200 rounded-full"
          onClick={handleLogoClick}
        >
          <h1 className="text-2xl font-semibold tracking-tight">DeductTax</h1>
        </button>
        <nav className="hidden sm:flex space-x-6 text-sm">
          <a href="#features" className="hover:text-blue-600 transition-colors">
            Features
          </a>
          <a href="#about" className="hover:text-blue-600 transition-colors">
            About
          </a>
          <a href="#contact" className="hover:text-blue-600 transition-colors">
            Contact
          </a>
        </nav>
      </header>

      {/* Main Content */}
      <main className="flex-1 flex flex-col items-center justify-center text-center px-6">
        <motion.h1
          initial={{ opacity: 0, y: 50 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.3, duration: 1.0 }}
          className="text-4xl font-bold text-gray-800 mb-4 w-full"
        >
          <div className="border border-black h-[calc(100vh-240px)] py-12 px-8 flex items-start overflow-y-auto">
            {" "}
            <div
              className={`font-medium leading-relaxed ${
                aiResponse
                  ? "text-left w-[60vw]"
                  : "text-center w-full text-5xl font-bold"
              }`}
            >
              {aiResponse ? (
                <div className="prose prose-lg max-w-none text-xl text-black overflow-y-auto max-h-full">
                  <ReactMarkdown>{aiResponse}</ReactMarkdown>
                </div>
              ) : (
                <div className="text-black">Hi, Samuel Choi 😁</div>
              )}
            </div>
          </div>
        </motion.h1>
        <motion.h1
          initial={{ opacity: 0, y: 50 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.6, duration: 1.0 }}
          className="w-full relative"
        >
          <div className="relative">
            <textarea
              ref={textareaRef}
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              onKeyPress={handleKeyPress}
              placeholder="Start your deductions."
              className="w-[50vw] resize-none border-none outline-none bg-pink-50 px-4 py-4 rounded-lg text-black"
              rows={1}
              style={{ minHeight: "100px", maxHeight: "120px" }}
              onInput={adjustHeight}
            />
            <button
              onClick={sendMessage}
              disabled={isLoading}
              className="absolute bottom-2 right-2 bg-blue-500 text-white rounded-full px-3 py-1 text-sm hover:bg-blue-600 disabled:opacity-50"
            >
              {isLoading ? "Sending..." : "Send"}
            </button>
          </div>
        </motion.h1>
        {/* <motion.button
          initial={{ opacity: 0, y: 0 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.9, duration: 1.0 }}
          className=" px-2 py-2 bg-white text-black border border-pink-700 rounded-xl transition min-w-[50vw]"
        >
          <textarea
            ref={textareaRef}
            placeholder="Start your deductions."
            className="w-full resize-none border-none outline-none bg-transparent "
            rows={1}
            style={{ minHeight: "40px", maxHeight: "120px" }}
            onInput={adjustHeight}
          />
        </motion.button> */}
      </main>

      {/* Footer */}
      <footer className="w-full py-4 text-center text-xs text-gray-500">
        © {new Date().getFullYear()} TaxFinder. All rights reserved by Samuel.
      </footer>
    </motion.div>
  );
}
