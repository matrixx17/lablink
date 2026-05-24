FROM node:20-alpine

WORKDIR /app/compchem-dashboard

COPY compchem-dashboard/package.json ./
COPY compchem-dashboard/package-lock.json* ./

RUN npm install

COPY compchem-dashboard ./
COPY wetlab-dashboard ../wetlab-dashboard

EXPOSE 5173

CMD ["npm", "run", "dev"]
