# Dockerfile for Render PHP deployment
FROM php:8.3-apache

# Install dependencies
RUN apt-get update && apt-get install -y \
    libcurl4-openssl-dev \
    libzip-dev \
    unzip \
    && docker-php-ext-install curl pdo pdo_pgsql \
    && a2enmod rewrite

# Enable Apache modules
RUN a2enmod rewrite headers

# Copy application
WORKDIR /var/www/html
COPY . .

# Set permissions
RUN chown -R www-data:www-data /var/www/html \
    && chmod -R 755 /var/www/html

# Apache config for webhook
RUN echo '<VirtualHost *:80>' > /etc/apache2/sites-available/000-default.conf \
    && echo '    DocumentRoot /var/www/html' >> /etc/apache2/sites-available/000-default.conf \
    && echo '    <Directory /var/www/html>' >> /etc/apache2/sites-available/000-default.conf \
    && echo '        AllowOverride All' >> /etc/apache2/sites-available/000-default.conf \
    && echo '        Require all granted' >> /etc/apache2/sites-available/000-default.conf \
    && echo '    </Directory>' >> /etc/apache2/sites-available/000-default.conf \
    && echo '</VirtualHost>' >> /etc/apache2/sites-available/000-default.conf

# Expose port
EXPOSE 10000

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD curl -f http://localhost/ || exit 1

# Start Apache
CMD ["apache2-foreground"]
