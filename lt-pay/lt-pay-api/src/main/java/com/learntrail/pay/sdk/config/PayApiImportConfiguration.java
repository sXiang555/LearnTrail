package com.learntrail.pay.sdk.config;


import org.springframework.cloud.openfeign.EnableFeignClients;
import org.springframework.context.annotation.Configuration;

@Configuration
@EnableFeignClients(basePackages = "com.learntrail.pay.sdk.client")
public class PayApiImportConfiguration {

}