FROM php:8.1-cli

# Set working directory
WORKDIR /app

# Copy all files to container
COPY . .

# Expose Render port
EXPOSE 10000

# Start PHP built-in server
CMD ["php", "-S", "0.0.0.0:10000", "bot.php"]
