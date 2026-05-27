FROM node:20-alpine

WORKDIR /app/compchem-dashboard

COPY compchem-dashboard/package.json ./
COPY compchem-dashboard/package-lock.json* ./

RUN npm install

COPY compchem-dashboard ./
COPY wetlab-dashboard ../wetlab-dashboard

# The unified build imports wet-lab page source directly (see src/App.tsx).
# Those files live at /app/wetlab-dashboard and resolve bare imports up their
# own directory chain (/app/wetlab-dashboard/node_modules -> /app/node_modules),
# never reaching the only installed deps in /app/compchem-dashboard/node_modules.
# Expose a shared node_modules at the common parent so wet-lab files resolve
# the same (identical) dependency set. Both package.json files match.
RUN ln -sfn /app/compchem-dashboard/node_modules /app/node_modules

EXPOSE 5173

CMD ["npm", "run", "dev"]
