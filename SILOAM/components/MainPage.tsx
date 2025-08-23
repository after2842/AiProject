import React, { useRef, useEffect, useState } from "react";
import { motion } from "framer-motion";
import { useRouter } from "next/navigation";
import ReactMarkdown from "react-markdown";
import Image from "next/image";

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
          className="px-4 py-2 bg-white rounded-full"
          onClick={handleLogoClick}
        >
          <h1 className="text-2xl font-semibold tracking-tight text-pink-600">
            SILOAM
          </h1>
        </button>
        <div className="min-w-[40vw] rounded-full bg-white py-2 px-8 text-center text-gray-500">
          ALO yoga pants for women
        </div>
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
          <div className="border border-black h-[85vh] py-12 px-8 flex items-start overflow-y-auto">
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
                <div className="text-black">
                  <div className="text-5xl font-bold flex items-center justify-center gap-24">
                    {/* <Image
                      src="https://cdn.shopify.com/s/files/1/2185/2813/files/w54212r_00_b2_s1_a1_m18_1500x.jpg?v=1751452461"
                      alt="logo"
                      width={350}
                      height={100}
                      className="rounded-lg"
                    /> */}
                    <Image
                      src="https://cdn.shopify.com/s/files/1/2185/2813/files/A0590U_00000_b2_s1_a3_m143_750x.jpg?v=1752603195"
                      alt="logo"
                      width={350}
                      height={100}
                      className="rounded-lg"
                    />
                    <Image
                      src="https://cdn.shopify.com/s/files/1/2185/2813/files/W51298R_0001_b1_s1_a1_1_m224_750x.jpg?v=1748462924"
                      alt="logo"
                      width={450}
                      height={100}
                      className="rounded-lg"
                    />
                    <div className="flex flex-col justify-start gap-12 h-[50vh]">
                      {/* <svg
                        className="alo-logo-mobile mx-auto"
                        xmlns="http://www.w3.org/2000/svg"
                        width="53"
                        height="36"
                      >
                        <path d="M16.975 14.858h4.291v20.413h-4.291v-1.434A10.386 10.386 0 0 1 10.633 36C4.77 36 0 31.095 0 25.065S4.77 14.13 10.634 14.13c2.374 0 4.57.805 6.34 2.163zm0 10.212c0-3.598-2.845-6.526-6.342-6.526-3.497 0-6.341 2.928-6.341 6.526 0 3.6 2.844 6.527 6.342 6.527 3.496 0 6.34-2.928 6.34-6.527zM28.81 35.272h-4.29V0h4.291zm2.423-10.207c0-6.03 4.77-10.935 10.632-10.935 5.864 0 10.634 4.905 10.634 10.935S47.73 36 41.867 36c-5.864 0-10.633-4.905-10.633-10.935zm4.291.005c0 3.599 2.845 6.526 6.341 6.526 3.498 0 6.342-2.927 6.342-6.526 0-3.599-2.844-6.526-6.342-6.526-3.496 0-6.34 2.927-6.34 6.526z"></path>
                        <desc>Brand Name: Alo Yoga</desc>
                      </svg> */}

                      <div className="text-black text-5xl">
                        7/8 High-Waist Airbrush Legging
                      </div>
                      <div className="text-gray-800 text-3xl text-center">
                        $129.00
                      </div>
                      <div className="text-black text-3xl">
                        {[
                          "Small",
                          "Medium",
                          "Large",
                          "X-Large",
                          "XX-Large",
                        ].map((size, index) => (
                          <span
                            key={index}
                            className="inline-block border border-black rounded-full px-4 py-1 mx-2 hover:bg-pink-100 cursor-pointer"
                          >
                            {size}
                          </span>
                        ))}
                      </div>
                      <div className="text-gray-800 text-lg text-left">
                        <details className="group">
                          <summary className="cursor-pointer font-semibold">
                            Reviews
                          </summary>
                          <div className="mt-2 text-black">
                            The 7/8 High-Waist Airbrush Legging — all the
                            smoothing, sculpting benefits of the full-length
                            version, in a perfectly cropped package. So good for
                            studio & all-day cool for street, this look features
                            flat-locked seaming for comfort and functionality,
                            no side seams, and an on-trend high waist.
                          </div>
                        </details>
                      </div>
                    </div>
                  </div>
                </div>
              )}
            </div>
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
        © {new Date().getFullYear()} SILOAM. All rights reserved by Samuel.
      </footer>
    </motion.div>
  );
}
