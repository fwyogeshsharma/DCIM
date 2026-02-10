FROM node:20-alpine

WORKDIR /app

# Install dependencies
COPY package*.json ./
RUN npm ci --only=production

# Copy source code
COPY . .

# Build TypeScript
RUN npm install -g typescript ts-node
RUN npm run build

# Expose port
EXPOSE 3002

# Start the application
CMD ["node", "dist/index.js"]
