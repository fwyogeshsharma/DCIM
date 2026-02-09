import pptxgen from "pptxgenjs";
import fs from "fs";

const pres = new pptxgen();

// Professional color palette matching the Marp theme
const colors = {
  primary: "2563EB",      // Blue (from Marp h1/h2)
  accent: "DC2626",       // Red (from Marp strong)
  dark: "1F2937",         // Dark gray (text)
  white: "FFFFFF",
  lightGray: "F5F5F5",
  background: "FFFFFF"
};

// Read and parse the markdown file
const mdContent = fs.readFileSync("output/slides.md", "utf-8");

// Split by slide separator (---) and filter out frontmatter
const allSlides = mdContent.split(/^---$/m);

// Remove frontmatter (first section with marp config)
const slides = allSlides.slice(1).filter(slide => slide.trim().length > 0);

console.log(`Found ${slides.length} slides to convert`);

// Helper function to clean markdown formatting
function cleanMarkdown(text) {
  return text
    .replace(/\*\*(.+?)\*\*/g, "$1")  // Bold
    .replace(/\*(.+?)\*/g, "$1")       // Italic
    .replace(/`(.+?)`/g, "$1")         // Code
    .replace(/^#+\s+/gm, "")           // Headers
    .replace(/^[\*\-]\s+/gm, "• ")     // Bullets
    .trim();
}

// Helper function to check if it's a title slide
function isTitleSlide(content) {
  return content.includes("**Enterprise DCIM Solution**") ||
         content.includes("**Thank You**") ||
         content.includes("**Contact & Next Steps**");
}

// Helper function to check if it has columns
function hasColumns(content) {
  return content.includes("<div class=\"columns\">");
}

// Process each slide
slides.forEach((slideContent, idx) => {
  const slide = pres.addSlide();

  // Parse slide content
  const lines = slideContent.split("\n").filter(l => l.trim());

  // Check if it's a title slide
  if (isTitleSlide(slideContent)) {
    slide.background = { color: colors.dark };

    let yPos = 1.5;
    lines.forEach(line => {
      const cleaned = cleanMarkdown(line);
      if (!cleaned || cleaned.startsWith("<") || cleaned.startsWith("*Powered by")) return;

      if (line.includes("**Enterprise DCIM**") || line.includes("**Thank You**")) {
        slide.addText(cleaned, {
          x: 0.5, y: yPos, w: 9, h: 1.0,
          fontSize: 48, bold: true, color: colors.white,
          align: "center", fontFace: "Arial"
        });
        yPos += 1.2;
      } else if (line.startsWith("###")) {
        slide.addText(cleaned, {
          x: 0.5, y: yPos, w: 9, h: 0.6,
          fontSize: 28, color: colors.primary,
          align: "center", fontFace: "Arial"
        });
        yPos += 0.8;
      } else if (line.includes("Questions?")) {
        slide.addText(cleaned, {
          x: 0.5, y: yPos, w: 9, h: 0.8,
          fontSize: 32, bold: true, color: colors.white,
          align: "center", fontFace: "Arial"
        });
        yPos += 1.0;
      } else {
        slide.addText(cleaned, {
          x: 0.5, y: yPos, w: 9, h: 0.5,
          fontSize: 18, color: colors.white, italic: true,
          align: "center", fontFace: "Arial"
        });
        yPos += 0.6;
      }
    });
  }
  // Check if it has columns
  else if (hasColumns(slideContent)) {
    slide.background = { color: colors.background };

    // Extract title
    const titleLine = lines.find(l => l.startsWith("##"));
    if (titleLine) {
      slide.addText(cleanMarkdown(titleLine), {
        x: 0.5, y: 0.5, w: 9, h: 0.7,
        fontSize: 32, bold: true, color: colors.primary,
        fontFace: "Arial"
      });
    }

    // Extract column content
    const columnStart = lines.findIndex(l => l.includes("<div class=\"columns\">"));
    if (columnStart >= 0) {
      let leftContent = [];
      let rightContent = [];
      let isLeft = true;

      for (let i = columnStart + 1; i < lines.length; i++) {
        if (lines[i].includes("</div>")) {
          if (lines[i] === "</div>") isLeft = false;
          continue;
        }
        if (lines[i].includes("<div>")) continue;

        const cleaned = cleanMarkdown(lines[i]);
        if (cleaned) {
          if (isLeft) leftContent.push(cleaned);
          else rightContent.push(cleaned);
        }
      }

      // Add left column
      slide.addText(leftContent.join("\n"), {
        x: 0.5, y: 1.5, w: 4.3, h: 4.0,
        fontSize: 12, color: colors.dark,
        fontFace: "Arial", valign: "top"
      });

      // Add right column
      slide.addText(rightContent.join("\n"), {
        x: 5.2, y: 1.5, w: 4.3, h: 4.0,
        fontSize: 12, color: colors.dark,
        fontFace: "Arial", valign: "top"
      });
    }
  }
  // Regular content slide
  else {
    slide.background = { color: colors.background };

    // Extract title (## header)
    const titleLine = lines.find(l => l.startsWith("##"));
    let contentStartIdx = 0;

    if (titleLine) {
      slide.addText(cleanMarkdown(titleLine), {
        x: 0.5, y: 0.5, w: 9, h: 0.7,
        fontSize: 32, bold: true, color: colors.primary,
        fontFace: "Arial"
      });
      contentStartIdx = lines.indexOf(titleLine) + 1;
    }

    // Process content
    let yPos = 1.5;
    let contentLines = [];
    let isCodeBlock = false;

    for (let i = contentStartIdx; i < lines.length; i++) {
      const line = lines[i];

      // Skip HTML and empty lines
      if (line.startsWith("<") || !line.trim()) continue;

      // Handle code blocks (ASCII diagrams)
      if (line.startsWith("```")) {
        if (!isCodeBlock) {
          isCodeBlock = true;
          continue;
        } else {
          // End of code block - add it
          if (contentLines.length > 0) {
            slide.addText(contentLines.join("\n"), {
              x: 1.5, y: yPos, w: 7.0, h: 2.5,
              fontSize: 9, color: colors.dark,
              fontFace: "Courier New", valign: "top"
            });
            contentLines = [];
            yPos += 2.7;
          }
          isCodeBlock = false;
          continue;
        }
      }

      if (isCodeBlock) {
        contentLines.push(line);
        continue;
      }

      // Handle subsection headers (###)
      if (line.startsWith("###")) {
        if (contentLines.length > 0) {
          slide.addText(contentLines.join("\n"), {
            x: 0.7, y: yPos, w: 8.6, h: contentLines.length * 0.3,
            fontSize: 12, color: colors.dark,
            fontFace: "Arial", valign: "top"
          });
          yPos += contentLines.length * 0.3 + 0.2;
          contentLines = [];
        }

        slide.addText(cleanMarkdown(line), {
          x: 0.5, y: yPos, w: 9, h: 0.4,
          fontSize: 18, bold: true, color: colors.primary,
          fontFace: "Arial"
        });
        yPos += 0.5;
        continue;
      }

      // Accumulate regular content
      const cleaned = cleanMarkdown(line);
      if (cleaned) {
        contentLines.push(cleaned);
      }
    }

    // Add remaining content
    if (contentLines.length > 0) {
      const maxHeight = 5.5 - yPos;
      slide.addText(contentLines.join("\n"), {
        x: 0.7, y: yPos, w: 8.6, h: Math.min(contentLines.length * 0.35, maxHeight),
        fontSize: 12, color: colors.dark,
        fontFace: "Arial", valign: "top"
      });
    }
  }

  // Add page number
  slide.addText(`${idx + 1}`, {
    x: 9.2, y: 7.0, w: 0.3, h: 0.3,
    fontSize: 10, color: colors.dark,
    align: "right", fontFace: "Arial"
  });
});

// Save presentation
await pres.writeFile({ fileName: "output/DCIM_Technical_Presentation.pptx" });
console.log("✓ Presentation converted successfully: output/DCIM_Technical_Presentation.pptx");
